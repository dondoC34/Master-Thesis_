from news_learner import *
from synthetic_user import *
from tqdm import tqdm
from scipy.stats import t


def save_allocation_errors(learners_list):
    """
    Saves the allocation diversity bounds max errors for a given list of learners relative to
    ALL THE 3 DE-RANDOMIZATION TECHNIQUES rand_1, rand_2 and rand_3.
    Three files ".txt" are saved containing the just mentioned info.
    Call only if the method "measure_allocation_diversity_bounds_errors" of each learner
    in the list has been called before.
    :return: nothing
    """
    file = open("de-Rand-Performances/perf_rand_1.txt", "a")
    file2 = open("de-Rand-Performances/perf_rand_2.txt", "a")
    file3 = open("de-Rand-Performances/perf_rand_3.txt", "a")
    for learner in learners_list:
        file.write(str(learner.rand_1_errors[0]))
        file2.write(str(learner.rand_2_errors[0]))
        file3.write(str(learner.rand_3_errors[0]))
        for k in range(1, len(learner.rand_1_errors)):
            file.write("," + str(learner.rand_1_errors[k]))
            file2.write("," + str(learner.rand_2_errors[k]))
            file3.write("," + str(learner.rand_3_errors[k]))
        file.write(",")
        file2.write(",")
        file3.write(",")
    file.close()
    file2.close()
    file3.close()


def plot_allocation_errors():
    """
    Plots an histogram containing the amount of times a de-randomization techinque did that error (in percentage).
    The plot will contain the info about EACH de-randomization techinque.
    Call only if the "save_allocation_errors" has been called in precedence and its output files have been saved.
    :return: Nothing
    """
    final_result = []
    final_result2 = []
    final_result3 = []

    file = open("de-Rand-Performances/perf_rand_1.txt", "r")
    result = file.read().split(",")
    result.__delitem__(-1)
    result = list(map(float, result))
    final_result += result
    file.close()

    file = open("de-Rand-Performances/perf_rand_2.txt", "r")
    result2 = file.read().split(",")
    result2.__delitem__(-1)
    result2 = list(map(float, result2))
    final_result2 += result2
    file.close()

    file = open("de-Rand-Performances/perf_rand_3.txt", "r")
    result3 = file.read().split(",")
    result3.__delitem__(-1)
    result3 = list(map(float, result3))
    final_result3 += result3
    file.close()

    res = final_result
    res2 = final_result2
    res3 = final_result3
    plt.hist([res, res2, res3], rwidth=0.5, bins=10)
    plt.legend(labels=["de-Randomizator-1", "de-Randomizator-2", "de-Randomizator-3"])
    plt.title("DeRandomization Mean Error Distribution")
    plt.ylabel("Occurrences")
    plt.show()


if __name__ == "__main__":
    """
    Four learner are going to be intialized. Each learner uses a different technique to de-randomize the LP results 
    (except for the standard learner). The avg results of "iterations" experiments are shown in terms of avg quality 
    per page and avg number of category per page.
    Furthermore, the average slot promenance per category given in output by each learner is measured.     
    """

    real_slot_promenances = [0.7, 0.8, 0.5, 0.3, 0.2, 0.4, 0.3, 0.1]

    categories = ["cibo", "gossip", "politic"]
    diversity_percentage_for_category = 5
    promenance_percentage_value = diversity_percentage_for_category / 100 * sum(real_slot_promenances)
    allocation_diversity_bounds = (promenance_percentage_value, promenance_percentage_value, promenance_percentage_value)
    iterations = 10000
    user = SyntheticUser(23, "F", 80)
    user.user_quality_measure = [0.5, 0.6, 0.6, 0.45, 0.45, 0.4]
    news_per_category = len(real_slot_promenances)
    learner_rand_1 = NewsLearner(categories=categories,
                                 real_slot_promenances=real_slot_promenances,
                                 allocation_approach="LP",
                                 lp_rand_technique="rand_1",
                                 allocation_diversity_bounds=allocation_diversity_bounds,
                                 ads_allocation=False)

    learner_rand_2 = NewsLearner(categories=categories,
                                 real_slot_promenances=real_slot_promenances,
                                 allocation_approach="LP",
                                 lp_rand_technique="rand_2",
                                 allocation_diversity_bounds=allocation_diversity_bounds,
                                 ads_allocation=False)

    learner_rand_3 = NewsLearner(categories=categories,
                                 real_slot_promenances=real_slot_promenances,
                                 allocation_approach="LP",
                                 lp_rand_technique="rand_3",
                                 allocation_diversity_bounds=allocation_diversity_bounds,
                                 ads_allocation=False)

    standard_learner = NewsLearner(categories=categories,
                                   real_slot_promenances=real_slot_promenances,
                                   allocation_approach="standard",
                                   allocation_diversity_bounds=allocation_diversity_bounds,
                                   ads_allocation=False)

    # READ THE WEIGHTED BETA MATRIX FROM A FILE TO HAVE THE BETAS DISTRIBUTION BE DIFFERENT FROM JUST A UNIFORM
    learner_rand_1.read_weighted_beta_matrix_from_file(indexes=[(0, 0)], desinences=["1-2"], folder="Saved-News_W-Beta/")
    learner_rand_2.read_weighted_beta_matrix_from_file(indexes=[(0, 0)], desinences=["1-2"], folder="Saved-News_W-Beta/")
    learner_rand_3.read_weighted_beta_matrix_from_file(indexes=[(0, 0)], desinences=["1-2"], folder="Saved-News_W-Beta/")
    standard_learner.read_weighted_beta_matrix_from_file(indexes=[(0, 0)], desinences=["1-2"], folder="Saved-News_W-Beta/")

    # CREATE AND FILL THE NEWS POOL OF EACH LEARNER
    news_pool = []
    k = 0
    for category in categories:
        for id in range(0, news_per_category):
            news_pool.append(News(news_id=k,
                                  news_name=category + "-" + str(id)))
            k += 1

    learner_rand_1.fill_news_pool(news_pool)
    learner_rand_2.fill_news_pool(news_pool)
    learner_rand_3.fill_news_pool(news_pool)
    standard_learner.fill_news_pool(news_pool)

    # METRICS USED TO DISPLAY THE RESULTS
    page_reward_rand_1 = []
    page_reward_rand_2 = []
    page_reward_rand_3 = []
    page_reward_standard = []
    page_reward_ilp = []
    page_diversity_rand_1 = []
    page_diversity_rand_2 = []
    page_diversity_rand_3 = []
    page_diversity_ilp = []
    page_diversity_standard = []
    allocated_promenance_per_category_rand_1 = [0] * len(categories)
    allocated_promenance_per_category_rand_2 = [0] * len(categories)
    allocated_promenance_per_category_rand_3 = [0] * len(categories)
    allocated_promenance_per_category_standard = [0] * len(categories)
    allocated_promenance_per_category_ilp = [0] * len(categories)
    allocations_count_rand_1 = 0
    allocations_count_rand_2 = 0
    allocations_count_rand_3 = 0
    allocations_count_standard = 0
    allocations_count_ilp = 0
    sample_rand_1 = []
    sample_rand_2 = []
    sample_rand_3 = []

    # FOR EACH LEARNER, ALLOCATE A PAGE THEN COLLECT THE MEASURES
    for i in tqdm(range(1, iterations + 1)):

        news_category_in_page = [0] * len(categories)
        allocation_rewards = []
        if i % 4 == 0:
            promenance_per_category = [0] * len(categories)
            allocation = learner_rand_1.find_best_allocation(user=user, update_assignment_matrices=False)
            allocations_count_rand_1 += 1
            for elem in allocation:
                click = user.click_news(elem)
                reward = user.get_reward(elem)
                allocation_rewards.append(reward)
                category_index = categories.index(elem.news_category)
                news_category_in_page[category_index] = 1
                news_slot = allocation.index(elem)
                allocated_promenance_per_category_rand_1[category_index] += real_slot_promenances[news_slot]
                promenance_per_category[category_index] += real_slot_promenances[news_slot]
            sample_rand_1.append(promenance_per_category.copy())

            page_reward_rand_1.append(sum(np.array(allocation_rewards) * np.array(real_slot_promenances)))
            page_diversity_rand_1.append(sum(news_category_in_page))

        elif i % 4 == 1:
            promenance_per_category = [0] * len(categories)
            allocation = learner_rand_2.find_best_allocation(user=user, update_assignment_matrices=False)
            allocations_count_rand_2 += 1
            for elem in allocation:
                click = user.click_news(elem)
                reward = user.get_reward(elem)
                allocation_rewards.append(reward)
                category_index = categories.index(elem.news_category)
                news_category_in_page[category_index] = 1
                news_slot = allocation.index(elem)
                allocated_promenance_per_category_rand_2[category_index] += real_slot_promenances[news_slot]
                promenance_per_category[category_index] += real_slot_promenances[news_slot]
            sample_rand_2.append(promenance_per_category.copy())

            page_reward_rand_2.append(sum(np.array(allocation_rewards) * np.array(real_slot_promenances)))
            page_diversity_rand_2.append(sum(news_category_in_page))
        elif i % 4 == 2:
            allocation = standard_learner.find_best_allocation(user=user, update_assignment_matrices=False)
            allocations_count_standard += 1
            for elem in allocation:
                click = user.click_news(elem)
                reward = user.get_reward(elem)
                allocation_rewards.append(reward)
                category_index = categories.index(elem.news_category)
                news_category_in_page[category_index] = 1
                news_slot = allocation.index(elem)
                allocated_promenance_per_category_standard[category_index] += real_slot_promenances[news_slot]

            page_reward_standard.append(sum(np.array(allocation_rewards) * np.array(real_slot_promenances)))
            page_diversity_standard.append(sum(news_category_in_page))
        else:
            promenance_per_category = [0] * len(categories)
            allocation = learner_rand_3.find_best_allocation(user=user, update_assignment_matrices=False)
            allocations_count_rand_3 += 1
            for elem in allocation:
                click = user.click_news(elem)
                reward = user.get_reward(elem)
                allocation_rewards.append(reward)
                category_index = categories.index(elem.news_category)
                news_category_in_page[category_index] = 1
                news_slot = allocation.index(elem)
                allocated_promenance_per_category_rand_3[category_index] += real_slot_promenances[news_slot]
                promenance_per_category[category_index] += real_slot_promenances[news_slot]
            sample_rand_3.append(promenance_per_category.copy())

            page_reward_rand_3.append(sum(np.array(allocation_rewards) * np.array(real_slot_promenances)))
            page_diversity_rand_3.append(sum(news_category_in_page))

    # PRINT THE COLLECTED MEASURES, AFTER AVERAGING THEM
    print("Rand_1 quality metrics:")
    print("Avg page reward: " + str(np.mean(page_reward_rand_1)))
    print("Avg page diversity: " + str(np.mean(page_diversity_rand_1)))
    print("--------------------------------")
    print("Rand_2 quality metrics:")
    print("Avg page reward: " + str(np.mean(page_reward_rand_2)))
    print("Avg page diversity: " + str(np.mean(page_diversity_rand_2)))
    print("--------------------------------")
    print("Rand_3 quality metrics:")
    print("Avg page reward: " + str(np.mean(page_reward_rand_3)))
    print("Avg page diversity: " + str(np.mean(page_diversity_rand_3)))
    print("--------------------------------")
    print("Standard quality metrics:")
    print("Avg page reward: " + str(np.mean(page_reward_standard)))
    print("Avg page diversity: " + str(np.mean(page_diversity_standard)))
    print("--------------------------------")
    print("ILP allocation quality metrics:")
    print("Avg page reward: " + str(np.mean(page_reward_ilp)))
    print("Avg page diversity: " + str(np.mean(page_diversity_ilp)))
    print("--------------------------------")
    print("Allocation category lower bounds: " + str(allocation_diversity_bounds))
    print("Rand_1 Avg promenance per category: " + str(np.array(allocated_promenance_per_category_rand_1) * 1 / allocations_count_rand_1))
    print("Rand_2 Avg promenance per category: " + str(
        np.array(allocated_promenance_per_category_rand_2) * 1 / allocations_count_rand_2))
    print("Rand_3 Avg promenance per category: " + str(
        np.array(allocated_promenance_per_category_rand_3) * 1 / allocations_count_rand_3))
    print("Standard Avg promenance per category: " + str(
        np.array(allocated_promenance_per_category_standard) * 1 / allocations_count_standard))
    print("--------------- T test --------------------")
    file = open("de-Rand-Performances/de-rand-metrics.txt", "a")
    file.write("C= " + str(len(categories)) + ", L= " + str(len(real_slot_promenances)) + "Psi= " + str(diversity_percentage_for_category) + "\n")
    file.write(str(np.mean(page_reward_rand_1)) + "," + str(np.mean(page_diversity_rand_1) / len(categories) * 100) + "\n")
    file.write(str(np.mean(page_reward_rand_2)) + "," + str(np.mean(page_diversity_rand_2) / len(categories) * 100) + "\n")
    file.write(str(np.mean(page_reward_rand_3)) + "," + str(np.mean(page_diversity_rand_3) / len(categories) * 100) + "\n")
    file.write(str(np.mean(page_reward_standard)) + "," + str(np.mean(page_diversity_standard) / len(categories) * 100) + "\n")
    file.close()
    for i in range(len(categories)):
        x = [y[i] for y in sample_rand_1]
        mean = np.mean(x)
        std = np.sqrt(np.var(x))
        n = len(x)
        mean0 = allocation_diversity_bounds[i]

        T = (mean - mean0) / (std / np.sqrt(n))
        if T < t.ppf(0.05, n):
            print("We can state that the mean for the category " + categories[i] + " is significantly less than " +
                  str(allocation_diversity_bounds[i]) + " with confidence 95%, for what concerns algorithm de_rand1.")
        else:
            print("There is no enough evidence in the data to state that, for category " + categories[i] + ", the mean "
                  "is significantly less than " + str(allocation_diversity_bounds[i]) + " with confidence 95%, for what"
                  "conerns algorithm de_rand1.")

    for i in range(len(categories)):
        x = [y[i] for y in sample_rand_2]
        mean = np.mean(x)
        std = np.sqrt(np.var(x))
        n = len(x)
        mean0 = allocation_diversity_bounds[i]

        T = (mean - mean0) / (std / np.sqrt(n))
        if T < t.ppf(0.05, n):
            print("We can state that the mean for the category " + categories[i] + " is significantly less than " +
                  str(allocation_diversity_bounds[i]) + " with confidence 95%, for what concerns algorithm de_rand2.")
        else:
            print("There is no enough evidence in the data to state that, for category " + categories[i] + ", the mean "
                  "is significantly less than " + str(
                  allocation_diversity_bounds[i]) + " with confidence 95%, for what"
                                                    "conerns algorithm de_rand2.")

    # T-Test for the fulfillment in expectation of the diversity constraints
    for i in range(len(categories)):
        x = [y[i] for y in sample_rand_3]
        mean = np.mean(x)
        std = np.sqrt(np.var(x))
        n = len(x)
        mean0 = allocation_diversity_bounds[i]

        T = (mean - mean0) / (std / np.sqrt(n))
        if T < t.ppf(0.05, n):
            print("We can state that the mean for the category " + categories[i] + " is significantly less than " +
                  str(allocation_diversity_bounds[i]) + " with confidence 95%, for what concerns algorithm de_rand3.")
        else:
            print("There is no enough evidence in the data to state that, for category " + categories[i] + ", the mean "
                  "is significantly less than " + str(
                allocation_diversity_bounds[i]) + " with confidence 95%, for what"
                                                 "conerns algorithm de_rand3.")

